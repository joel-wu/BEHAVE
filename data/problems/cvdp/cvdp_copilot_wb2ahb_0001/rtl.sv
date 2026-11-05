module wishbone_to_ahb_bridge (
    input logic clk_i_level, rst_i, cyc_i, stb_i,
    input logic [3:0] sel_i,
    input logic we_i,
    input logic [31:0] addr_i, data_i,
    input logic hclk_level, hreset_n,
    input logic [31:0] hrdata,
    input logic [1:0] hresp,
    input logic hready,
    output logic [31:0] data_o,
    output logic ack_o, err_o,
    output logic [1:0] htrans,
    output logic [2:0] hsize, hburst,
    output logic hwrite,
    output logic [31:0] haddr, hwdata
);
    wire reset_n = rst_i & hreset_n;
    logic [1:0] wb_state=0, ahb_state=0;
    logic request=0, completed=0;
    (* ASYNC_REG="TRUE" *) logic done1=0, done2=0, req1=0, req2=0;
    logic [2:0] req_size=0;
    logic [31:0] req_addr=0, req_data=0, req_mask=0;
    logic req_write=0, response_error=0;
    logic [31:0] response_data=0;
    logic valid_sel;
    logic [2:0] size;
    logic [1:0] offset;
    logic [31:0] mask;
    function automatic [31:0] reversed(input logic [31:0] x);
        reversed={x[7:0],x[15:8],x[23:16],x[31:24]};
    endfunction
    initial begin
        data_o=0; ack_o=0; err_o=0;
        hsize=0; hwrite=0; haddr=0; hwdata=0;
    end
    assign htrans=ahb_state==1 ? 2'b10 : 2'b00;
    assign hburst=0;
    always_comb begin
        valid_sel=1; size=0; offset=0; mask=0;
        case(sel_i)
            1: mask=32'h000000ff;
            2: begin mask=32'h0000ff00; offset=1; end
            4: begin mask=32'h00ff0000; offset=2; end
            8: begin mask=32'hff000000; offset=3; end
            3: begin mask=32'h0000ffff; size=1; end
            12: begin mask=32'hffff0000; size=1; offset=2; end
            15: begin mask=32'hffffffff; size=2; end
            default: valid_sel=0;
        endcase
    end
    always_ff @(posedge clk_i_level or negedge reset_n) begin
        if(!reset_n) begin
            wb_state<=0; request<=0; done1<=0; done2<=0;
            req_size<=0; req_addr<=0; req_data<=0; req_mask<=0; req_write<=0;
            data_o<=0; ack_o<=0; err_o<=0;
        end else begin
            done1<=completed; done2<=done1;
            ack_o<=0; err_o<=0;
            case(wb_state)
                0: if(cyc_i && stb_i) begin
                    if(valid_sel) begin
                        req_size<=size; req_addr<={addr_i[31:2],offset};
                        req_mask<=mask; req_data<=reversed(data_i & mask); req_write<=we_i;
                        request<=~request; wb_state<=1;
                    end else begin data_o<=0; err_o<=1; wb_state<=2; end
                end
                1: if(done2==request) begin
                    data_o<=response_data; wb_state<=2;
                    if(cyc_i && stb_i) begin
                        ack_o<=!response_error; err_o<=response_error;
                    end
                end
                2: wb_state<=0;
                default: wb_state<=0;
            endcase
        end
    end
    always_ff @(posedge hclk_level or negedge reset_n) begin
        if(!reset_n) begin
            req1<=0; req2<=0; completed<=0; ahb_state<=0;
            response_data<=0; response_error<=0;
            hsize<=0; hwrite<=0; haddr<=0; hwdata<=0;
        end else begin
            req1<=request; req2<=req1;
            case(ahb_state)
                0: if(req2!=completed) begin
                    hsize<=req_size; haddr<=req_addr; hwdata<=req_data;
                    hwrite<=req_write; ahb_state<=1;
                end
                1: if(hready) ahb_state<=2;
                2: if(hready) begin
                    response_data <= (hresp!=0 || req_write) ? 0 : (reversed(hrdata) & req_mask);
                    response_error<=hresp!=0; completed<=req2; ahb_state<=0;
                end
                default: ahb_state<=0;
            endcase
        end
    end
endmodule
