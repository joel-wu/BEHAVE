module microcode_sequencer (
    input logic clk, init_reset,
    input logic [4:0] instr_in,
    input logic [3:0] d_in,
    input logic c_n_in, c_inc_in, r_en, cc, ien, oen,
    output logic [3:0] d_out,
    output logic c_n_out, c_inc_out, full, empty
);
    logic [3:0] pc_q, aux_q, push_word, pop_word, pop_result;
    logic [3:0] words [0:15];
    logic [4:0] count;
    // 0 idle; 1 PUSH commit; 2 POP read; 3 POP commit.
    logic [1:0] phase;
    wire enabled = !cc && !ien;
    wire [4:0] arithmetic_sum = {1'b0,aux_q}+{1'b0,d_in}+{4'b0,c_n_in};
    wire [4:0] increment_sum = {1'b0,pc_q}+{4'b0,c_inc_in};
    assign full=(count==16);
    assign empty=(count==0);
    always_comb begin
        d_out=0; c_n_out=0; c_inc_out=0;
        if(enabled) begin
            case(instr_in)
                1: begin d_out=pc_q; c_inc_out=(phase==0) && increment_sum[4]; end
                2: d_out=aux_q;
                3: d_out=d_in;
                4: begin d_out=arithmetic_sum[3:0]; c_n_out=arithmetic_sum[4]; end
                14: d_out=pop_result;
                default: begin end
            endcase
        end
        if(oen) d_out=0;
    end
    always_ff @(posedge clk) begin
        if(init_reset) begin
            pc_q<=0; aux_q<=0; count<=0; phase<=0;
            push_word<=0; pop_word<=0; pop_result<=0;
            for(int i=0;i<16;i++) words[i]<=0;
        end else begin
            if(!r_en) aux_q<=d_in;
            if(enabled && instr_in==0) begin
                pc_q<={3'b0,c_inc_in}; count<=0; phase<=0; pop_result<=0;
            end else if(phase==1) begin
                if(count<16) begin words[count]<=push_word; count<=count+5'd1; end
                phase<=0;
            end else if(phase==2) begin
                pop_word<=count==0 ? 4'd0 : words[count-5'd1];
                phase<=3;
            end else if(phase==3) begin
                pop_result<=pop_word;
                if(count>0) count<=count-5'd1;
                phase<=0;
            end else if(enabled) begin
                case(instr_in)
                    1: pc_q<=increment_sum[3:0];
                    11: begin push_word<=pc_q; phase<=1; end
                    14: phase<=2;
                    default: begin end
                endcase
            end
        end
    end
endmodule
