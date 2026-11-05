module cipher (
    input logic clk, rst_n, start,
    input logic [31:0] data_in,
    input logic [15:0] key,
    output logic [31:0] data_out,
    output logic done
);
    logic [1:0] state;
    logic [2:0] round_index;
    logic [15:0] left_q,right_q,key_q,subkey,x0,x1,x2,f_value;
    assign subkey={key_q[14:0],key_q[15]} ^ {13'd0,round_index};
    assign x0=right_q ^ subkey;
    assign x1={x0[12:0],x0[15:13]};
    assign x2=x1+subkey;
    assign f_value={x2[14:0],x2[15]};
    always_ff @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            state<=0; round_index<=0; left_q<=0; right_q<=0; key_q<=0;
            data_out<=0; done<=0;
        end else begin
            done<=0;
            case(state)
                0: if(start) begin
                    left_q<=data_in[31:16]; right_q<=data_in[15:0]; key_q<=key;
                    round_index<=0; state<=1;
                end
                1: begin
                    left_q<=right_q; right_q<=left_q ^ f_value;
                    if(round_index==7) state<=2;
                    else round_index<=round_index+1'b1;
                end
                2: begin data_out<={right_q,left_q}; done<=1; state<=0; end
                default: state<=0;
            endcase
        end
    end
endmodule
