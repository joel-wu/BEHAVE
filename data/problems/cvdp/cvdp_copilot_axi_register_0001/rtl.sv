module axi_register #(
    parameter int ADDR_WIDTH=32, parameter int DATA_WIDTH=32
) (
    input logic clk_i, rst_n_i,
    input logic [ADDR_WIDTH-1:0] awaddr_i,
    input logic awvalid_i,
    input logic [DATA_WIDTH-1:0] wdata_i,
    input logic wvalid_i,
    input logic [DATA_WIDTH/8-1:0] wstrb_i,
    input logic bready_i,
    input logic [ADDR_WIDTH-1:0] araddr_i,
    input logic arvalid_i, rready_i, done_i,
    output logic awready_o, wready_o,
    output logic [1:0] bresp_o,
    output logic bvalid_o, arready_o,
    output logic [DATA_WIDTH-1:0] rdata_o,
    output logic rvalid_o,
    output logic [1:0] rresp_o,
    output logic [19:0] beat_o,
    output logic start_o, writeback_o
);
    logic [19:0] beat;
    logic start, writeback, done_status, aw_held, w_held;
    logic [11:0] saved_addr;
    logic [DATA_WIDTH-1:0] saved_data;
    logic [DATA_WIDTH/8-1:0] saved_strobe;
    wire commit=aw_held && w_held && !bvalid_o;
    assign awready_o=!aw_held && !bvalid_o;
    assign wready_o=!w_held && !bvalid_o;
    assign arready_o=!rvalid_o;
    assign beat_o=beat;
    assign start_o=start;
    assign writeback_o=writeback;

    always_ff @(posedge clk_i or negedge rst_n_i) begin
        if (!rst_n_i) begin
            beat<=0; start<=0; writeback<=0; done_status<=0;
            aw_held<=0; w_held<=0; saved_addr<=0; saved_data<=0; saved_strobe<=0;
            bvalid_o<=0; bresp_o<=0; rvalid_o<=0; rresp_o<=0; rdata_o<=0;
        end else begin
            if (bvalid_o && bready_i) bvalid_o<=0;
            if (rvalid_o && rready_i) rvalid_o<=0;
            if (arready_o && arvalid_i) begin
                rvalid_o<=1; rdata_o<=0; rresp_o<=0;
                case (araddr_i[11:0])
                    12'h100: rdata_o<=DATA_WIDTH'(beat);
                    12'h200: rdata_o<=DATA_WIDTH'(start);
                    12'h300: rdata_o<=DATA_WIDTH'(done_status);
                    12'h400: rdata_o<=DATA_WIDTH'(writeback);
                    12'h500: rdata_o<=DATA_WIDTH'(32'h00010001);
                    default: rresp_o<=2'b10;
                endcase
            end
            done_status<=done_status|done_i;
            if (commit) begin
                aw_held<=0; w_held<=0; bvalid_o<=1; bresp_o<=0;
                case (saved_addr)
                    12'h100: if (&saved_strobe) beat<=20'(saved_data);
                    12'h200: if (&saved_strobe) start<=saved_data[0];
                    12'h300: if ((&saved_strobe) && saved_data[0]) done_status<=0;
                    12'h400: if (&saved_strobe) writeback<=saved_data[0];
                    default: bresp_o<=2'b10;
                endcase
            end
            if (awready_o && awvalid_i) begin saved_addr<=awaddr_i[11:0]; aw_held<=1; end
            if (wready_o && wvalid_i) begin
                saved_data<=wdata_i; saved_strobe<=wstrb_i; w_held<=1;
            end
        end
    end
endmodule
