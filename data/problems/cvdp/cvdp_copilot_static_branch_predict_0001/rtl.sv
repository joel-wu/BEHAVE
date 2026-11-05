module static_branch_predict (
    input logic [31:0] fetch_rdata_i, fetch_pc_i,
    input logic fetch_valid_i,
    output logic predict_branch_taken_o,
    output logic [31:0] predict_branch_pc_o
);
    logic [31:0] offset;
    logic [6:0] opcode;
    assign opcode=fetch_rdata_i[6:0];
    always_comb begin
        case (opcode)
            7'h6f: offset={{11{fetch_rdata_i[31]}},fetch_rdata_i[31],fetch_rdata_i[19:12],fetch_rdata_i[20],fetch_rdata_i[30:21],1'b0};
            7'h63: offset={{19{fetch_rdata_i[31]}},fetch_rdata_i[31],fetch_rdata_i[7],fetch_rdata_i[30:25],fetch_rdata_i[11:8],1'b0};
            7'h67: offset={{20{fetch_rdata_i[31]}},fetch_rdata_i[31:20]};
            default: offset=0;
        endcase
    end
    assign predict_branch_pc_o=fetch_pc_i+offset;
    assign predict_branch_taken_o=fetch_valid_i &&
        ((opcode==7'h6f) || (opcode==7'h67) || (opcode==7'h63 && offset[31]));
endmodule
