module load_store_unit (
    input logic clk, rst_n, dmem_gnt_i, dmem_rvalid_i,
    input logic [31:0] dmem_rsp_rdata_i,
    input logic ex_if_req_i, ex_if_we_i,
    input logic [1:0] ex_if_type_i,
    input logic [31:0] ex_if_wdata_i, ex_if_addr_base_i, ex_if_addr_offset_i,
    output logic dmem_req_o,
    output logic [31:0] dmem_req_addr_o,
    output logic dmem_req_we_o,
    output logic [3:0] dmem_req_be_o,
    output logic [31:0] dmem_req_wdata_o,
    output logic ex_if_ready_o,
    output logic [31:0] wb_if_rdata_o,
    output logic wb_if_rvalid_o
);
    logic [1:0] state;
    logic [31:0] address, payload;
    logic we;
    logic [3:0] lanes;
    wire [31:0] effective_address = ex_if_addr_base_i + ex_if_addr_offset_i;
    logic [3:0] selected_lanes;
    always_comb begin
        selected_lanes=0;
        case (ex_if_type_i)
            0: selected_lanes=4'b0001 << effective_address[1:0];
            1: if (!effective_address[0]) selected_lanes=4'b0011 << effective_address[1:0];
            2: if (effective_address[1:0]==0) selected_lanes=4'b1111;
            default: selected_lanes=0;
        endcase
    end
    assign dmem_req_o = state==1;
    assign dmem_req_addr_o = state==1 ? address : 0;
    assign dmem_req_we_o = state==1 && we;
    assign dmem_req_be_o = state==1 ? lanes : 0;
    assign dmem_req_wdata_o = state==1 ? payload : 0;
    assign ex_if_ready_o = state==0;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state<=0; address<=0; payload<=0; we<=0; lanes<=0;
            wb_if_rdata_o<=0; wb_if_rvalid_o<=0;
        end else begin
            wb_if_rvalid_o<=0;
            case (state)
                0: if (ex_if_req_i && selected_lanes!=0) begin
                    address<=effective_address; we<=ex_if_we_i;
                    payload<=ex_if_we_i ? ex_if_wdata_i : 0;
                    lanes<=selected_lanes; state<=1;
                end
                1: if (dmem_gnt_i) state<=we ? 0 : 2;
                2: if (dmem_rvalid_i) begin
                    wb_if_rdata_o<=dmem_rsp_rdata_i; wb_if_rvalid_o<=1; state<=0;
                end
                default: state<=0;
            endcase
        end
    end
endmodule
